// Pure helpers extracted from Pending.jsx to keep the page file under
// the per-page LOC budget. No React here — only payload parsing, draft
// construction, and presentational utilities for queued Zotero changes.

import { formatWhen } from '../utils/stepModelStatus.js';

export const UNBOUND_GENERATED_TARGET_MESSAGE = 'This note has no Zotero target. Reject it or regenerate the review.';

export function hasUnboundGeneratedTarget(change) {
  return Boolean(change.generated_preview && !change.generated_preview.target);
}

export const MISSING_APPROVAL_MESSAGE = 'Exact change not available. Reload and check it before applying.';

export function hasApprovalPreview(change) {
  const preview = change.approval_preview;
  return !hasUnboundGeneratedTarget(change)
    && preview?.version === 1
    && preview.change_id === change.id
    && preview.item_key === change.item_key
    && preview.change_type === change.change_type
    && preview.payload !== null && typeof preview.payload === 'object' && !Array.isArray(preview.payload)
    && Number.isInteger(preview.edit_revision) && preview.edit_revision >= 0
    && preview.target !== null && typeof preview.target === 'object' && !Array.isArray(preview.target)
    && typeof preview.preview_sha256 === 'string' && /^[a-f0-9]{64}$/.test(preview.preview_sha256);
}

export function approvalPreviews(changes) {
  if (!changes.length || !changes.every(hasApprovalPreview)) return null;
  return Object.fromEntries(changes.map((change) => [change.id, change.approval_preview.preview_sha256]));
}

// Server times are UTC, either ISO or SQLite's "YYYY-MM-DD HH:MM:SS"; show them in the reader's zone as "11 Nov, 05:59".
export function localTime(value) {
  const raw = String(value || '').trim();
  const iso = /^\d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?$/.test(raw) ? `${raw.replace(' ', 'T')}Z` : raw;
  return formatWhen(iso) || raw;
}

// Identical queued rows for one paper (same type and visible summary) are re-queued versions of the same
// change: the newest (the API lists newest first) stays visible, older pending ones fold behind a disclosure.
export function collapseStaleChanges(changes) {
  const rows = [];
  const byKey = new Map();
  for (const change of changes) {
    const key = change.status === 'pending' ? `${change.change_type}|${changeSummary(change)}` : null;
    const head = key && byKey.get(key);
    if (head) head.older.push(change);
    else {
      const row = { change, older: [] };
      rows.push(row);
      if (key) byKey.set(key, row);
    }
  }
  return rows;
}

export const STATUS_TABS = [
  { value: 'pending', label: 'Pending' },
  { value: 'applied', label: 'Applied' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'failed', label: 'Failed' },
  { value: 'applying', label: 'Needs checking' },
];

export const statusLabel = (status) => STATUS_TABS.find((tab) => tab.value === status)?.label || status;

export function flattenCollections(nodes, depth = 0) {
  const flat = [];
  for (const node of nodes || []) {
    flat.push({
      key: node.key,
      name: node.name,
      item_count: node.item_count || 0,
      depth,
    });
    if (node.children?.length) {
      flat.push(...flattenCollections(node.children, depth + 1));
    }
  }
  return flat;
}

export function stripHtml(input) {
  return String(input || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

export function parseCommaTags(text) {
  return String(text || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function readPayload(change) {
  try {
    if (typeof change.payload_json === 'string') {
      return JSON.parse(change.payload_json || '{}');
    }
    return change.payload_json || {};
  } catch {
    return {};
  }
}

export function buildDraft(change) {
  const payload = readPayload(change);
  if (change.change_type === 'tag_changes') {
    return {
      add_tags_text: (payload.add_tags || []).join(', '),
      remove_tags_text: (payload.remove_tags || []).join(', '),
    };
  }
  if (
    change.change_type === 'add_to_collection'
    || change.change_type === 'remove_from_collection'
  ) {
    return {
      collection_key: String(payload.collection_key || '').trim(),
      collection_path: String(payload.collection_path || payload.collection_name || '').trim(),
    };
  }
  if (change.change_type === 'add_note') {
    return {
      note_title: String(payload.note_title || 'Triage note').trim(),
      note_html: String(payload.note_html || '').trim(),
    };
  }
  return {};
}

export function previewChange(change) {
  try {
    const payload = readPayload(change);
    if (change.change_type === 'tag_changes') {
      const added = (payload.add_tags || []).join(', ');
      const removed = (payload.remove_tags || []).join(', ');
      return `add=[${added}] remove=[${removed}]`;
    }
    if (change.change_type === 'add_note') {
      return `${payload.note_title || 'Triage note'} :: ${stripHtml(payload.note_html || '').slice(0, 100)}`;
    }
    if (change.change_type === 'add_to_collection') {
      const target = String(
        payload.collection_path || payload.collection_name || payload.collection_key || '',
      ).trim();
      return `add_to_collection=[${target}]`;
    }
    if (change.change_type === 'remove_from_collection') {
      const target = String(
        payload.collection_path || payload.collection_name || payload.collection_key || '',
      ).trim();
      return `remove_from_collection=[${target}]`;
    }
    return JSON.stringify(payload).slice(0, 140);
  } catch {
    return String(change.payload_json || '');
  }
}

// One readable line for the change target (the badge already names the action).
export function changeSummary(change) {
  const payload = readPayload(change);
  const collection = String(payload.collection_path || payload.collection_name || payload.collection_key || '').trim();
  switch (change.change_type) {
    case 'tag_changes': {
      const added = (payload.add_tags || []).map((t) => `+${t}`);
      const removed = (payload.remove_tags || []).map((t) => `−${t}`);
      return [...added, ...removed].join('  ') || 'No tag changes';
    }
    case 'add_to_collection':
    case 'remove_from_collection':
      return collection || 'No collection chosen';
    case 'add_note':
    case 'upsert_note': {
      // Notes queued before the band was dropped from the heading still carry « · band highlight».
      const excerpt = stripHtml(payload.note_html).replace(/ · band \w+/g, '').slice(0, 90);
      return `“${payload.note_title || 'Triage note'}”${excerpt ? ` — ${excerpt}` : ''}`;
    }
    default:
      return previewChange(change);
  }
}

export function changeBadgeClass(type) {
  switch (type) {
    case 'tag_changes':
      return 'bg-amber-100 text-amber-800 border border-amber-300';
    case 'add_to_collection':
      return 'bg-emerald-100 text-emerald-800 border border-emerald-300';
    case 'remove_from_collection':
      return 'bg-rose-100 text-rose-800 border border-rose-300';
    case 'add_note':
      return 'bg-sky-100 text-sky-800 border border-sky-300';
    default:
      return 'bg-slate-100 text-slate-700 border border-slate-300';
  }
}

// Same color vocabulary as changeBadgeClass(), expressed as the shared
// ActionBadge `tone` token so the change pill renders via the app-wide Badge
// primitive instead of a bespoke className. The amber/emerald/rose/sky/slate
// mapping is byte-for-byte the palette changeBadgeClass() used.
export function changeBadgeTone(type) {
  switch (type) {
    case 'tag_changes':
      return 'amber';
    case 'add_to_collection':
      return 'emerald';
    case 'remove_from_collection':
      return 'rose';
    case 'add_note':
      return 'sky';
    default:
      return 'slate';
  }
}

const CHANGE_TYPE_LABELS = {
  tag_changes: 'Change tags',
  add_to_collection: 'Add to collection',
  remove_from_collection: 'Remove from collection',
  add_note: 'Add note',
  upsert_note: 'Add or update note',
};

export function changeTypeLabel(type) {
  return CHANGE_TYPE_LABELS[type] || String(type || '').replace(/_/g, ' ');
}

export function collectionOptionLabel(entry) {
  const indent = '— '.repeat(entry.depth || 0);
  return `${indent}${entry.name} (${entry.item_count})`;
}

export function buildPayloadFromDraft(change, draftsForId, flatCollections) {
  const draft = { ...buildDraft(change), ...(draftsForId || {}) };
  if (change.change_type === 'tag_changes') {
    return {
      add_tags: parseCommaTags(draft.add_tags_text || ''),
      remove_tags: parseCommaTags(draft.remove_tags_text || ''),
    };
  }
  if (
    change.change_type === 'add_to_collection'
    || change.change_type === 'remove_from_collection'
  ) {
    const collectionKey = String(draft.collection_key || '').trim();
    const entry = (flatCollections || []).find((n) => String(n.key) === collectionKey);
    const collectionPath = String(entry?.name || draft.collection_path || '').trim();
    return { collection_key: collectionKey, collection_path: collectionPath };
  }
  if (change.change_type === 'add_note') {
    return {
      note_title: String(draft.note_title || '').trim(),
      note_html: String(draft.note_html || '').trim(),
    };
  }
  return null;
}
